"""diag_multi13.py — 14 стратегий: WB 498/price, OZON 403 composer, FP 404, AV 429."""
import json, re, sys, time, os
from datetime import datetime

results = {}
t0 = time.time()

def log(tag, msg):
    print(f"[{tag}] {msg}", flush=True)

def safe_json(data):
    if isinstance(data, dict):
        return {k: safe_json(v) for k, v in data.items()}
    if isinstance(data, list):
        return [safe_json(x) for x in data]
    if isinstance(data, (int, float, str, bool)) or data is None:
        return data
    return str(data)

try:
    from curl_cffi import requests as cffi_req
    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False
    import requests as cffi_req

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
JSON_H = {"Accept": "application/json, text/plain, */*", "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8", "Referer": "https://www.wildberries.ru/", "Origin": "https://www.wildberries.ru"}
HTML_H = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8", "Referer": "https://www.wildberries.ru/"}

def get(url, headers=None, timeout=12, **kw):
    if HAS_CFFI:
        k = {"impersonate": "chrome", "timeout": timeout}
        if headers: k["headers"] = headers
        k.update(kw)
        return cffi_req.get(url, **k)
    h = {"User-Agent": UA}
    if headers: h.update(headers)
    return cffi_req.get(url, headers=h, timeout=timeout, **kw)

# ============================================================
# WB: nm_id=1097975275
# ============================================================
WB_NM = 1097975275
WB_VOL = WB_NM // 100000  # 10
WB_PART = WB_NM // 1000   # 1097
WB_BASKET = "basket-42.wbbasket.ru"

# --- WB:1 price.json from basket CDN (new path) ---
try:
    t = time.time()
    for sub in ["price", "prices", "price-ru", "info/ru/price"]:
        url = f"https://{WB_BASKET}/vol{WB_VOL}/part{WB_PART}/{WB_NM}/{sub}.json"
        r = get(url, JSON_H, timeout=8)
        if r.status_code == 200:
            data = r.json() if r.content else {}
            log("WB:1", f"price.json OK sub={sub} keys={list(data.keys())[:8]}")
            results["WB:1_price_json"] = {"ok": True, "detail": f"sub={sub} keys={list(data.keys())[:10]}", "s": round(time.time()-t,1)}
            break
        if r.status_code == 404:
            continue
        log("WB:1", f"sub={sub} HTTP={r.status_code}")
    else:
        results["WB:1_price_json"] = {"ok": False, "detail": "all sub-paths 404", "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:1_price_json"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}

# --- WB:2 HTML with X-Requested-With + mobile ---
try:
    t = time.time()
    url = f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx"
    h = {**HTML_H, "X-Requested-With": "XMLHttpRequest", "sec-fetch-dest": "document", "sec-fetch-mode": "navigate", "sec-fetch-site": "same-origin", "sec-fetch-user": "?1", "Upgrade-Insecure-Requests": "1"}
    r = get(url, h, timeout=18)
    html = r.text if r.status_code == 200 else ""
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200 and html:
        price_re = re.compile(r'"(?:price|priceU|priceD|basic|product)"\s*:\s*(\d{4,})')
        pf = price_re.findall(html)
        if pf: detail += f" prices={pf[:6]}"; ok = True
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        og = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if og: detail += f" og={og.group(1)}"; ok = True
        with open("d13_wb_html.html", "w", encoding="utf-8") as f:
            f.write(html[:30000])
    elif r.status_code == 498:
        detail += " (498=WB custom block)"
        # Save body for analysis
        with open("d13_wb_498.txt", "w", encoding="utf-8") as f:
            f.write(html[:5000])
    results["WB:2_html_xrw"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:2_html_xrw"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:2", results.get("WB:2_html_xrw", {}).get("detail","?"))

# --- WB:3 mobile.m.wildberries.ru ---
try:
    t = time.time()
    url = f"https://m.wildberries.ru/catalog/{WB_NM}/detail.aspx"
    h = {**HTML_H, "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"}
    r = get(url, h, timeout=15)
    html = r.text if r.status_code == 200 else ""
    detail = f"HTTP={r.status_code} len={len(r.content)} url={r.url[:60]}"
    ok = False
    if html:
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        price_re = re.compile(r'"(?:price|basic|product)"\s*:\s*(\d{4,})')
        pf = price_re.findall(html)
        if pf: detail += f" prices={pf[:5]}"; ok = True
    results["WB:3_mobile"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:3_mobile"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:3", results.get("WB:3_mobile", {}).get("detail","?"))

# --- WB:4 card.wb.ru with dest=0 (no region) + spp=10 ---
try:
    t = time.time()
    for dest in [0, -1257786, 1]:
        for spp in [10, 30]:
            url = f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest={dest}&spp={spp}&nm={WB_NM}"
            r = get(url, JSON_H, timeout=10)
            if r.status_code == 200:
                data = r.json()
                prods = data.get("data", {}).get("items", []) or data.get("products", [])
                if prods:
                    p = prods[0]
                    sizes = p.get("sizes", [])
                    pr = sizes[0].get("price", {}) if sizes else {}
                    log("WB:4", f"OK dest={dest} spp={spp} price={json.dumps(pr)[:100]}")
                    results["WB:4_card_dest"] = {"ok": True, "detail": f"dest={dest} spp={spp} price={json.dumps(pr)[:150]}", "s": round(time.time()-t,1)}
                    break
            time.sleep(0.5)
        if "WB:4_card_dest" in results:
            break
    else:
        results["WB:4_card_dest"] = {"ok": False, "detail": f"all combos failed, last HTTP={r.status_code}", "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:4_card_dest"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}

# --- WB:5 static-basket.wbbasket.ru (CDN mirror) ---
try:
    t = time.time()
    url = f"https://static-basket-{WB_VOL:02d}.wbbasket.ru/vol{WB_VOL}/part{WB_PART}/{WB_NM}/info/ru/card.json"
    r = get(url, JSON_H, timeout=10)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        # Check for price in sizes
        sizes = data.get("sizes", [])
        if sizes:
            pr = sizes[0].get("price", {})
            if pr:
                detail += f" sizes[0].price={json.dumps(pr)[:100]}"
                ok = True
        # Check top-level
        for k in ("price", "priceU", "priceD", "oldPrice"):
            if k in data:
                detail += f" {k}={data[k]}"
                ok = True
    results["WB:5_static_basket"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:5_static_basket"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:5", results.get("WB:5_static_basket", {}).get("detail","?"))

# --- WB:6 search.wb.ru (alternative price endpoint) ---
try:
    t = time.time()
    url = f"https://search.wb.ru/cards/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}"
    r = get(url, JSON_H, timeout=10)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        prods = data.get("data", {}).get("items", []) or data.get("products", [])
        if prods:
            p = prods[0]
            sizes = p.get("sizes", [])
            pr = sizes[0].get("price", {}) if sizes else {}
            detail += f" price={json.dumps(pr)[:150]}"
            ok = True
        else:
            detail += f" keys={list(data.keys())[:8]}"
    results["WB:6_search_wb"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["WB:6_search_wb"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("WB:6", results.get("WB:6_search_wb", {}).get("detail","?"))

# ============================================================
# OZON: sku=3481320800
# ============================================================
OZ_SKU = "3481320800"
OZ_URL = f"https://www.ozon.ru/product/malenkiy-prints-i-lis-nabor-dobryh-letnih-otkrytok-dlya-postkrossinga-pozdravleniy-i-motivatsii-{OZ_SKU}/"

# --- OZ:1 Composer with proper x-o3 headers ---
try:
    t = time.time()
    ts = str(int(time.time()))
    url = f"https://api.ozon.ru/composer-api.bx/page/json/v2?url=/product/{OZ_SKU}/"
    h = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": UA,
        "Referer": "https://www.ozon.ru/",
        "Origin": "https://www.ozon.ru",
        "x-o3-app-version": "1",
        "x-request-number": "1",
        "x-request-ts": ts,
        "x-cdn": "1",
    }
    r = get(url, h, timeout=15)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        blocks = data.get("page", {}).get("blocks", [])
        for b in blocks if isinstance(blocks, list) else []:
            if isinstance(b, dict) and "price" in str(b.get("type","")).lower():
                props = b.get("props", {}) or b.get("data", {})
                pr = props.get("price") or props
                if isinstance(pr, dict):
                    for pk in ("price","current","basic","value","min","total"):
                        pv = pr.get(pk)
                        if isinstance(pv,(int,float)) and pv>0:
                            detail += f" {pk}={pv}"
                            ok = True
        if not ok:
            detail += f" top={list(data.keys())[:6]}"
    results["OZ:1_composer_xo3"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:1_composer_xo3"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:1", results.get("OZ:1_composer_xo3", {}).get("detail","?"))

# --- OZ:2 PDP entrypoint with widgetStates ---
try:
    t = time.time()
    ts = str(int(time.time()))
    widget_states = json.dumps({"webPrice":{"state":{},"props":{}},"webAddToCart":{"state":{},"props":{}},"webGallery":{"state":{},"props":{}},"webDescription":{"state":{},"props":{}},"breadCrumbs":{"state":{},"props":{}}})
    url = f"https://api.ozon.ru/composer-api.bx/page/entrypoint/v2?_pageId=pdp&_state={widget_states}&url=%2Fproduct%2F{OZ_SKU}%2F"
    h = {
        "Accept": "application/json",
        "User-Agent": UA,
        "Referer": OZ_URL,
        "Origin": "https://www.ozon.ru",
        "x-o3-app-version": "1",
        "x-request-number": "1",
        "x-request-ts": ts,
    }
    r = get(url, h, timeout=15)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        txt = json.dumps(data)[:8000]
        pm = re.findall(r'"(?:price|current|basic|total)"\s*:\s*(\d{3,})', txt)
        if pm: detail += f" prices={pm[:6]}"; ok = True
        # Look for webPrice block
        wp = re.search(r'"webPrice".*?"price"\s*:\s*(\d+)', txt)
        if wp: detail += f" wp={wp.group(1)}"; ok = True
    results["OZ:2_entrypoint"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:2_entrypoint"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:2", results.get("OZ:2_entrypoint", {}).get("detail","?"))

# --- OZ:3 Warm-up session (homepage → product) ---
try:
    t = time.time()
    if HAS_CFFI:
        sess = cffi_req.Session(impersonate="chrome")
    else:
        sess = cffi_req.Session()
    # Warm-up
    r1 = sess.get("https://www.ozon.ru/", timeout=10, headers={"User-Agent": UA, "Accept": "text/html"})
    cookies = dict(sess.cookies)
    time.sleep(1.5)
    # Product page
    r2 = sess.get(OZ_URL, timeout=18, headers={"User-Agent": UA, "Accept": "text/html", "Referer": "https://www.ozon.ru/"})
    html = r2.text
    detail = f"warmup={r1.status_code} cookies={list(cookies.keys())[:5]} | prod={r2.status_code} len={len(r2.content)}"
    ok = False
    if r2.status_code == 200:
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        for lb in ld_blocks:
            try:
                ld = json.loads(lb.strip())
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict): offers = [offers]
                    for o in offers:
                        if isinstance(o, dict) and o.get("price"):
                            detail += f" LD={o['price']} {o.get('priceCurrency','?')}"
                            ok = True
            except: pass
        vis = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        with open("d13_ozon_html.html", "w", encoding="utf-8") as f:
            f.write(html[:30000])
    results["OZ:3_warmup_session"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:3_warmup_session"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:3", results.get("OZ:3_warmup_session", {}).get("detail","?"))

# --- OZ:4 mobile.ozon.ru ---
try:
    t = time.time()
    url = f"https://mobile.ozon.ru/product/{OZ_SKU}/"
    h = {"Accept": "text/html", "Accept-Language": "ru-RU,ru;q=0.9", "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"}
    r = get(url, h, timeout=15)
    html = r.text if r.status_code == 200 else ""
    detail = f"HTTP={r.status_code} len={len(r.content)} url={r.url[:60]}"
    ok = False
    if html:
        vis = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        for lb in ld_blocks:
            try:
                ld = json.loads(lb.strip())
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict): offers = [offers]
                    for o in offers:
                        if isinstance(o, dict) and o.get("price"):
                            detail += f" LD={o['price']}"; ok = True
            except: pass
    results["OZ:4_mobile"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:4_mobile"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:4", results.get("OZ:4_mobile", {}).get("detail","?"))

# --- OZ:5 Playwright with full browser context ---
try:
    t = time.time()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
        )
        # Remove navigator.webdriver
        ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = ctx.new_page()
        # Warm-up
        page.goto("https://www.ozon.ru/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
        # Product
        resp = page.goto(OZ_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(4000)
        html = page.content()
        status = resp.status if resp else 0
        detail = f"HTTP={status} len={len(html)}"
        ok = False
        # Check for price in rendered DOM
        price_el = page.query_selector('[data-widget="webPrice"]')
        if price_el:
            txt = price_el.inner_text()
            detail += f" webPrice='{txt[:80]}'"
            pm = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', txt)
            if pm: detail += f" prices={pm[:5]}"; ok = True
        # JSON-LD
        ld_blocks = re.findall(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL)
        for lb in ld_blocks:
            try:
                ld = json.loads(lb.strip())
                if isinstance(ld, dict) and ld.get("@type") == "Product":
                    offers = ld.get("offers", {})
                    if isinstance(offers, dict): offers = [offers]
                    for o in offers:
                        if isinstance(o, dict) and o.get("price"):
                            detail += f" LD={o['price']}"; ok = True
            except: pass
        # Visible prices in full HTML
        vis = re.findall(r'(\d[\d\s\u00a0]{1,9})\s*₽', html)
        if vis: detail += f" vis={vis[:6]}"; ok = True
        with open("d13_ozon_pw.html", "w", encoding="utf-8") as f:
            f.write(html[:40000])
        browser.close()
    results["OZ:5_pw_full"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["OZ:5_pw_full"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("OZ:5", results.get("OZ:5_pw_full", {}).get("detail","?"))

# ============================================================
# FARPOST: 138859452
# ============================================================
FP_ID = "138859452"

# --- FP:1 Search API to verify listing exists ---
try:
    t = time.time()
    url = f"https://www.farpost.ru/api/search/?query={FP_ID}&city=vladivostok"
    h = {"Accept": "application/json", "User-Agent": UA}
    r = get(url, h, timeout=12)
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if r.status_code == 200:
        data = r.json()
        items = data.get("items", []) or data.get("results", []) or data.get("data", [])
        detail += f" items={len(items) if isinstance(items,list) else '?'}"
        if isinstance(items, list) and items:
            detail += f" first={json.dumps(items[0])[:100]}"
            ok = True
    results["FP:1_search_api"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["FP:1_search_api"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("FP:1", results.get("FP:1_search_api", {}).get("detail","?"))

# --- FP:2 Multiple URL patterns ---
try:
    t = time.time()
    patterns = [
        f"https://www.farpost.ru/bulletin/{FP_ID}/",
        f"https://www.farpost.ru/bulletin/{FP_ID}",
        f"https://www.farpost.ru/announcement/{FP_ID}",
        f"https://www.farpost.ru/vladivostok/bulletin/{FP_ID}",
        f"https://www.farpost.ru/{FP_ID}.h",
        f"https://www.farpost.ru/vladivostok/{FP_ID}.h/",
    ]
    last_status = 0
    ok = False
    detail_parts = []
    for p in patterns:
        r = get(p, {"Accept": "text/html", "User-Agent": UA}, timeout=10)
        last_status = r.status_code
        detail_parts.append(f"{p.split('.ru')[1][:30]}={r.status_code}")
        if r.status_code == 200 and len(r.content) > 5000:
            html = r.text
            vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
            if vis:
                detail_parts.append(f"vis={vis[:3]}")
                ok = True
                break
            # Check if it's a real page (not 404 page)
            if "404" not in html[:2000].lower() and "не найдено" not in html[:2000].lower():
                detail_parts.append(f"200_len={len(r.content)}")
                ok = True
                with open("d13_fp_html.html", "w", encoding="utf-8") as f:
                    f.write(html[:20000])
                break
        time.sleep(0.5)
    results["FP:2_url_patterns"] = {"ok": ok, "detail": " | ".join(detail_parts), "s": round(time.time()-t,1)}
except Exception as e:
    results["FP:2_url_patterns"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("FP:2", results.get("FP:2_url_patterns", {}).get("detail","?"))

# --- FP:3 API v2 (announcement by id) ---
try:
    t = time.time()
    for api_url in [
        f"https://api.farpost.ru/api/v2/announcement/{FP_ID}",
        f"https://www.farpost.ru/api/v2/announcement/{FP_ID}",
        f"https://www.farpost.ru/api/announcement/{FP_ID}",
    ]:
        r = get(api_url, {"Accept": "application/json", "User-Agent": UA}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            detail = f"HTTP=200 url={api_url.split('.ru')[1][:40]} keys={list(data.keys())[:8]}"
            results["FP:3_api_v2"] = {"ok": True, "detail": detail, "s": round(time.time()-t,1)}
            break
        time.sleep(0.3)
    else:
        results["FP:3_api_v2"] = {"ok": False, "detail": f"all API 404/err, last={r.status_code}", "s": round(time.time()-t,1)}
except Exception as e:
    results["FP:3_api_v2"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("FP:3", results.get("FP:3_api_v2", {}).get("detail","?"))

# --- FP:4 Check 404 page content (is listing really gone?) ---
try:
    t = time.time()
    r = get(f"https://www.farpost.ru/bulletin/{FP_ID}", {"Accept": "text/html", "User-Agent": UA}, timeout=12)
    html = r.text
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    # Extract title and key text
    title_m = re.search(r'<title[^>]*>([^<]+)</title>', html, re.I)
    if title_m: detail += f" title='{title_m.group(1)[:60]}'"
    # Check for "объявление удалено" or similar
    for phrase in ["удалено", "не найдено", "не существует", "истекло", "архив", "404"]:
        if phrase.lower() in html.lower():
            detail += f" found='{phrase}'"
    # Save snippet
    with open("d13_fp_404.html", "w", encoding="utf-8") as f:
        f.write(html[:10000])
    results["FP:4_404_content"] = {"ok": r.status_code == 200, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["FP:4_404_content"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("FP:4", results.get("FP:4_404_content", {}).get("detail","?"))

# ============================================================
# AVITO: 429 CAPTCHA
# ============================================================
AV_URL = "https://www.avito.ru/vladivostok/smartfony-Ad0VKgIAOAMQwAAAKoC_AgAeAM0.html"

# --- AV:1 Warm-up + delay + specific headers ---
try:
    t = time.time()
    if HAS_CFFI:
        sess = cffi_req.Session(impersonate="chrome")
    else:
        sess = cffi_req.Session()
    # Warm-up: homepage
    r1 = sess.get("https://www.avito.ru/", timeout=10, headers={"User-Agent": UA, "Accept": "text/html"})
    time.sleep(3)  # Longer delay
    # Product
    r2 = sess.get(AV_URL, timeout=15, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Referer": "https://www.avito.ru/vladivostok/smartfony",
        "sec-ch-ua": '"Chromium";v="131", "Not_A Brand";v="24"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "Upgrade-Insecure-Requests": "1",
    })
    html = r2.text
    detail = f"warmup={r1.status_code} cookies={list(dict(sess.cookies).keys())[:4]} | prod={r2.status_code} len={len(r2.content)}"
    ok = False
    if r2.status_code == 200:
        dp = re.findall(r'data-price=["\']?(\d+)["\']?', html)
        if dp: detail += f" dp={dp[:3]}"; ok = True
        og = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if og: detail += f" og={og.group(1)}"; ok = True
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        if "captcha" in html.lower() or "hcaptcha" in html.lower():
            detail += " CAPTCHA"
        with open("d13_av_html.html", "w", encoding="utf-8") as f:
            f.write(html[:20000])
    elif r2.status_code == 429:
        detail += " 429 (rate limited)"
    results["AV:1_warmup_delay"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["AV:1_warmup_delay"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("AV:1", results.get("AV:1_warmup_delay", {}).get("detail","?"))

# --- AV:2 Playwright (full browser, no headless detection) ---
try:
    t = time.time()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1920, "height": 1080},
            locale="ru-RU",
            timezone_id="Asia/Vladivostok",
        )
        ctx.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru', 'en']});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            window.chrome = {runtime: {}};
        """)
        page = ctx.new_page()
        # Warm-up
        page.goto("https://www.avito.ru/", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(4000)
        # Product
        resp = page.goto(AV_URL, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(5000)
        html = page.content()
        status = resp.status if resp else 0
        detail = f"HTTP={status} len={len(html)}"
        ok = False
        # Price in DOM
        price_el = page.query_selector('[data-automation-id="price"]') or page.query_selector('.price')
        if price_el:
            txt = price_el.inner_text()
            detail += f" price_el='{txt[:40]}'"
            pm = re.findall(r'(\d[\d\s\u00a0]{2,9})', txt)
            if pm: detail += f" p={pm[:3]}"; ok = True
        # og:price
        og = re.search(r'og:price:amount["\'][^>]*content=["\']([\d.]+)', html, re.I)
        if og: detail += f" og={og.group(1)}"; ok = True
        # Visible
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        # Captcha check
        if "captcha" in html.lower() or "hcaptcha" in html.lower():
            detail += " CAPTCHA"
        with open("d13_av_pw.html", "w", encoding="utf-8") as f:
            f.write(html[:30000])
        browser.close()
    results["AV:2_playwright"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["AV:2_playwright"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("AV:2", results.get("AV:2_playwright", {}).get("detail","?"))

# --- AV:3 Mobile API (m.avito.ru) ---
try:
    t = time.time()
    url = f"https://m.avito.ru/vladivostok/smartfony-Ad0VKgIAOAMQwAAAKoC_AgAeAM0.html"
    h = {
        "Accept": "text/html",
        "Accept-Language": "ru-RU,ru;q=0.9",
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
    }
    r = get(url, h, timeout=15)
    html = r.text if r.status_code == 200 else ""
    detail = f"HTTP={r.status_code} len={len(r.content)}"
    ok = False
    if html:
        vis = re.findall(r'(\d[\d\s\u00a0]{2,9})\s*(?:₽|руб)', html)
        if vis: detail += f" vis={vis[:5]}"; ok = True
        if "captcha" in html.lower(): detail += " CAPTCHA"
    results["AV:3_mobile"] = {"ok": ok, "detail": detail, "s": round(time.time()-t,1)}
except Exception as e:
    results["AV:3_mobile"] = {"ok": False, "detail": f"ERR {e}", "s": round(time.time()-t,1)}
log("AV:3", results.get("AV:3_mobile", {}).get("detail","?"))

# ============================================================
# SAVE
# ============================================================
results["total_s"] = round(time.time() - t0, 1)
results["time"] = datetime.now().isoformat()

with open("d13_results.json", "w", encoding="utf-8") as f:
    json.dump(safe_json(results), f, ensure_ascii=False, indent=2)

print(f"\n=== TOTAL: {results['total_s']}s ===", flush=True)
print(json.dumps(safe_json(results), ensure_ascii=False, indent=2), flush=True)
