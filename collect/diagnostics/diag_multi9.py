#!/usr/bin/env python3
"""diag_multi9.py — WB price via cffi session + PW, FP live bulletin + PW verify, AV PW 8s."""
import json, re, time, logging, os
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("d9")
BASE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HDRS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "ru-RU,ru;q=0.9"}
results = {}
t0 = time.time()

def save(name, data):
    results[name] = data
    log.info("  -> %s: ok=%s %s", name, data.get("ok"), str(data.get("detail",""))[:300])

def extract_prices(html):
    prices = []
    for m in re.finditer(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.S|re.I):
        try:
            ld = json.loads(m.group(1).strip())
            items = ld if isinstance(ld, list) else [ld]
            for item in items:
                if isinstance(item, dict):
                    off = item.get("offers")
                    if isinstance(off, dict):
                        p = off.get("price") or off.get("lowPrice")
                        if p: prices.append(float(p))
                    elif isinstance(off, list):
                        for o in off:
                            p = o.get("price")
                            if p: prices.append(float(p))
        except: pass
    for m in re.finditer(r'"price"\s*:\s*"?(\d{2,})"?', html):
        prices.append(float(m.group(1)))
    for m in re.finditer(r'og:price:amount["\']\s+content=["\']([\d.]+)', html, re.I):
        prices.append(float(m.group(1)))
    # WB specific: "basic": 12345
    for m in re.finditer(r'"basic"\s*:\s*(\d{3,})', html):
        prices.append(float(m.group(1)))
    # Visual: >12 345 ₽<
    for m in re.findall(r'>(\d[\d\s]{3,10})\s*₽\s*<', html[:200000]):
        prices.append(float(m.group(1).replace(' ','').replace('\xa0','')))
    return prices

# ============================================================
# WILDBERRIES
# ============================================================
log.info("=== WILDBERRIES ===")
WB_NM = 1097975275

# WB1: curl_cffi session — warmup + card_v2
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/", "Origin": "https://www.wildberries.ru", "User-Agent": UA}
    
    # Warmup: get HTML page (collects cookies)
    hr = s.get(f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx", timeout=15,
               headers={"Accept": "text/html", "User-Agent": UA})
    log.info("WB warmup: HTTP=%d len=%d", hr.status_code, len(hr.content))
    
    # Extract from HTML
    html_wb = hr.text
    prices_wb = extract_prices(html_wb)
    og_m = re.search(r'og:price:amount[^>]*content=["\']([\d.]+)', html_wb, re.I)
    basic_m = re.findall(r'"basic"\s*:\s*(\d+)', html_wb)
    
    with open(os.path.join(BASE, "d9_wb_html.html"), "w", encoding="utf-8") as f:
        f.write(html_wb)
    
    save("WB:cffi_html", {
        "ok": len(prices_wb) > 0,
        "detail": f"HTTP={hr.status_code} len={len(html_wb)} prices={prices_wb[:5]} og={og_m.group(1) if og_m else None} basic={basic_m[:3]}",
        "s": round(time.time()-t0,1)
    })
    
    # Then try card_v2 with session cookies
    r = s.get(f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&spp=30&nm={WB_NM}",
              timeout=15, headers=H)
    if r.status_code == 200 and r.text.startswith("{"):
        j = r.json()
        prods = j.get("data",{}).get("products",[])
        pp = []
        if prods:
            p = prods[0]
            po = p.get("price",{})
            pp = [po.get("basic",0), po.get("percent",0), p.get("salepriceU",0), p.get("priceU",0)]
            # Also check sizes
            sizes = p.get("sizes",[])
            if sizes:
                sp = sizes[0].get("price",{})
                pp.extend([sp.get("basic",0), sp.get("product",0)])
        save("WB:cffi_card_v2", {
            "ok": any(x > 0 for x in pp),
            "detail": f"HTTP=200 n={len(prods)} prices={pp[:8]}",
            "s": round(time.time()-t0,1)
        })
        if prods:
            with open(os.path.join(BASE, "d9_wb_card_v2.json"), "w", encoding="utf-8") as f:
                json.dump(prods[0], f, ensure_ascii=False, indent=2)
    else:
        save("WB:cffi_card_v2", {
            "ok": False,
            "detail": f"HTTP={r.status_code} first80={r.text[:80]}",
            "s": round(time.time()-t0,1)
        })
except Exception as e:
    save("WB:cffi_html", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB2: Playwright — longer wait, handle challenge
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="ru-RU", viewport={"width":1366,"height":768})
        page = ctx.new_page()
        
        # Capture XHR for price
        xhr_prices = []
        def on_resp(resp):
            try:
                if resp.status == 200 and ("card.wb.ru" in resp.url or "catalog" in resp.url or "price" in resp.url):
                    ct = resp.headers.get("content-type","")
                    if "json" in ct:
                        body = resp.text()
                        pp = re.findall(r'"basic"\s*:\s*(\d+)', body)
                        pp2 = re.findall(r'"price"\s*:\s*"?(\d+)', body)
                        if pp or pp2:
                            xhr_prices.append({"url": resp.url[:80], "basic": pp[:3], "price": pp2[:3]})
            except: pass
        page.on("response", on_resp)
        
        page.goto(f"https://www.wildberries.ru/catalog/{WB_NM}/detail.aspx", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(10000)  # 10s — дать загрузиться
        
        # Check if challenge page
        title_m = re.search(r'<title>(.*?)</title>', page.content(), re.S)
        title = title_m.group(1).strip() if title_m else "?"
        
        html = page.content()
        prices = extract_prices(html)
        
        # Try to find price in DOM
        price_dom = []
        try:
            els = page.query_selector_all('[class*="price"], [data-price], .c-main__price')
            for el in els[:5]:
                txt = el.inner_text().strip()
                if txt:
                    price_dom.append(txt[:30])
        except: pass
        
        with open(os.path.join(BASE, "d9_wb_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        save("WB:playwright_10s", {
            "ok": len(prices) > 0 or price_dom,
            "detail": f"len={len(html)} title={title[:50]} prices={prices[:5]} dom={price_dom[:3]} xhr={xhr_prices[:3]}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("WB:playwright_10s", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# WB3: basket CDN (correct formula from dfv3)
try:
    from curl_cffi import requests as cr
    vol = WB_NM // 100000  # 10979
    part = WB_NM // 1000    # 1097975
    path = f"/vol{vol}/part{part}/{WB_NM}/info/ru/card.json"
    H = {"Accept": "application/json", "Referer": "https://www.wildberries.ru/"}
    
    for n in range(1, 61):
        host = f"basket-{n:02d}.wbbasket.ru"
        url = f"https://{host}{path}"
        try:
            r = cr.get(url, impersonate="chrome", timeout=5, headers=H)
            if r.status_code == 200:
                d = r.json()
                if d.get("nm_id") == WB_NM:
                    save("WB:basket_cdn", {
                        "ok": True,
                        "detail": f"host={host} vol={vol} part={part} title={d.get('imt_name','')[:50]} keys={len(d)}",
                        "s": round(time.time()-t0,1)
                    })
                    with open(os.path.join(BASE, "d9_wb_basket.json"), "w", encoding="utf-8") as f:
                        json.dump(d, f, ensure_ascii=False, indent=2)
                    break
        except: pass
    else:
        save("WB:basket_cdn", {"ok": False, "detail": f"60 hosts, vol={vol} part={part}", "s": round(time.time()-t0,1)})
except Exception as e:
    save("WB:basket_cdn", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# FARPOST — find live bulletin + PW
# ============================================================
log.info("=== FARPOST ===")

# FP1: Find live bulletin via category page
fp_live_id = None
try:
    from curl_cffi import requests as cr
    s = cr.Session(impersonate="chrome")
    
    # Try electronics category
    for cat_url in [
        "https://www.farpost.ru/vladivostok/electronics/",
        "https://www.farpost.ru/vladivostok/",
        "https://www.farpost.ru/vladivostok/mobile-phones/",
    ]:
        r = s.get(cat_url, timeout=15)
        log.info("FP category %s: HTTP=%d len=%d", cat_url.split('/')[-2] if '/' in cat_url.split('/')[-1] else cat_url, r.status_code, len(r.text))
        
        # Find bulletin links: /vladivostok/123456789.html
        links = re.findall(r'href=["\'](?:/)?vladivostok/(\d{8,12})\.html["\']', r.text)
        # Also: href="/vladivostok/123456789/"
        links2 = re.findall(r'href=["\'](?:/)?vladivostok/(\d{8,12})/["\']', r.text)
        all_ids = list(dict.fromkeys(links + links2))
        log.info("FP %s: %d unique IDs", cat_url, len(all_ids))
        
        if all_ids:
            fp_live_id = all_ids[0]
            # Save some for later
            with open(os.path.join(BASE, "d9_fp_ids.json"), "w") as f:
                json.dump(all_ids[:20], f)
            break
        # Save HTML for debug
        with open(os.path.join(BASE, f"d9_fp_cat_{cat_url.split('/')[-2]}.html"), "w", encoding="utf-8") as f:
            f.write(r.text)
except Exception as e:
    log.info("FP category err: %s", e)

# FP2: PW to live bulletin (handle verify)
fp_pw_result = None
try:
    from playwright.sync_api import sync_playwright
    target_id = fp_live_id or "138952176"
    target_url = f"https://www.farpost.ru/vladivostok/{target_id}.html"
    
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, locale="ru-RU", viewport={"width":1366,"height":768})
        page = ctx.new_page()
        
        # Warmup
        page.goto("https://www.farpost.ru/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        
        # Go to bulletin
        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)
        
        # Check if we're on verify page
        cur_url = page.url
        is_verify = "verify" in cur_url
        
        # If verify, try to click through or wait
        if is_verify:
            log.info("FP: on verify page, waiting...")
            page.wait_for_timeout(5000)
            # Try clicking any button/link
            try:
                btn = page.query_selector("button, a.btn, [class*='verify'], [class*='check']")
                if btn:
                    btn.click()
                    page.wait_for_timeout(3000)
            except: pass
            cur_url = page.url
            is_verify = "verify" in cur_url
        
        html = page.content()
        prices = extract_prices(html)
        title_m = re.search(r'<title>(.*?)</title>', html, re.S)
        title = title_m.group(1).strip() if title_m else "?"
        
        # FP-specific: look for price in various places
        fp_price_patterns = {
            "data-price": re.findall(r'data-price=["\']?(\d+)', html),
            "price-class": re.findall(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>\s*([\d\s]+)', html)[:5],
            "json-ld": bool(re.search(r'application/ld\+json', html, re.I)),
            "og-price": re.findall(r'og:price[^>]*content=["\']([\d.]+)', html),
            "rub-symbol": re.findall(r'(\d[\d\s]{2,10})\s*₽', html[:100000])[:5],
        }
        
        with open(os.path.join(BASE, "d9_fp_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        fp_pw_result = {
            "ok": len(prices) > 0 or fp_price_patterns["data-price"] or fp_price_patterns["rub-symbol"],
            "detail": f"ID={target_id} url={cur_url[:80]} len={len(html)} title={title[:60]} prices={prices[:5]} pats={fp_price_patterns}",
            "s": round(time.time()-t0,1)
        }
        save("FP:playwright", fp_pw_result)
        browser.close()
except Exception as e:
    save("FP:playwright", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# FP3: Direct requests to live ID
try:
    from curl_cffi import requests as cr
    target_id = fp_live_id or "138952176"
    r = cr.get(f"https://www.farpost.ru/vladivostok/{target_id}.html", impersonate="chrome", timeout=15)
    html = r.text
    prices = extract_prices(html)
    title_m = re.search(r'<title>(.*?)</title>', html, re.S)
    title = title_m.group(1).strip() if title_m else "?"
    is_home = "доска объявлений" in title.lower()
    
    save("FP:cffi_direct", {
        "ok": len(prices) > 0 and not is_home,
        "detail": f"ID={target_id} HTTP={r.status_code} len={len(html)} title={title[:60]} prices={prices[:3]} home={is_home}",
        "s": round(time.time()-t0,1)
    })
except Exception as e:
    save("FP:cffi_direct", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# AVITO — PW desktop 8s + XHR
# ============================================================
log.info("=== AVITO ===")
AV_ID = "1154321987"

try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent=UA, locale="ru-RU", viewport={"width":1366,"height":768}
        )
        page = ctx.new_page()
        
        xhr_data = []
        def on_resp(resp):
            try:
                if resp.status == 200:
                    ct = resp.headers.get("content-type","")
                    if "json" in ct:
                        body = resp.text()
                        if "price" in body.lower() or "offer" in body.lower():
                            pp = re.findall(r'"price"\s*:\s*"?(\d+)', body)
                            pp2 = re.findall(r'"amount"\s*:\s*"?(\d+)', body)
                            if pp or pp2:
                                xhr_data.append({"url": resp.url[:100], "price": pp[:5], "amount": pp2[:3]})
            except: pass
        page.on("response", on_resp)
        
        page.goto(f"https://www.avito.ru/vladivostok/{AV_ID}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(8000)
        
        html = page.content()
        prices = extract_prices(html)
        
        # Avito patterns
        av_pats = {
            "data-price": re.findall(r'data-price=["\']?(\d+)', html),
            "meta-og": re.findall(r'og:price:amount["\']\s+content=["\']([\d.]+)', html),
            "price-el": re.findall(r'class=["\'][^"\']*price[^"\']*["\'][^>]*>([^<]{1,30})', html)[:5],
            "json-state": bool(re.search(r'window\.__INITIAL|__PRELOADED|__APP_DATA|window\.__data', html)),
            "captcha": bool(re.search(r'hcaptcha|recaptcha|captcha|antibot', html, re.I)),
            "rub": re.findall(r'(\d[\d\s]{2,10})\s*₽', html[:150000])[:5],
        }
        
        with open(os.path.join(BASE, "d9_avito_pw.html"), "w", encoding="utf-8") as f:
            f.write(html)
        
        save("AV:playwright_8s", {
            "ok": len(prices) > 0 or av_pats["data-price"] or av_pats["rub"],
            "detail": f"len={len(html)} prices={prices[:5]} pats={av_pats} xhr={xhr_data[:3]}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("AV:playwright_8s", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# AV2: m.avito.ru (mobile)
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
            locale="ru-RU", viewport={"width":390,"height":844}, is_mobile=True, has_touch=True
        )
        page = ctx.new_page()
        page.goto(f"https://m.avito.ru/vladivostok/{AV_ID}", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(6000)
        html = page.content()
        prices = extract_prices(html)
        av_pats = {
            "data-price": re.findall(r'data-price=["\']?(\d+)', html),
            "rub": re.findall(r'(\d[\d\s]{2,10})\s*₽', html[:100000])[:5],
            "captcha": bool(re.search(r'captcha|antibot', html, re.I)),
        }
        with open(os.path.join(BASE, "d9_avito_m.html"), "w", encoding="utf-8") as f:
            f.write(html)
        save("AV:playwright_m", {
            "ok": len(prices) > 0 or av_pats["data-price"] or av_pats["rub"],
            "detail": f"len={len(html)} prices={prices[:5]} pats={av_pats}",
            "s": round(time.time()-t0,1)
        })
        browser.close()
except Exception as e:
    save("AV:playwright_m", {"ok": False, "detail": f"err={e}", "s": round(time.time()-t0,1)})

# ============================================================
# SAVE
# ============================================================
out = {"time": datetime.now().isoformat(), "total_s": round(time.time()-t0,1), "results": results}
out_path = os.path.join(BASE, "d9_results.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
log.info("Saved to %s", out_path)
log.info("TOTAL: %.1fs", time.time()-t0)
